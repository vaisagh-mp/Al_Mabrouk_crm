
// Side Bar
jQuery(function ($) {
  // Sidebar toggle logic
  $(".sidebar-dropdown > a").click(function () {
    $(".sidebar-submenu").slideUp(200);
    if ($(this).parent().hasClass("active")) {
      $(".sidebar-dropdown").removeClass("active");
      $(this).parent().removeClass("active");
    } else {
      $(".sidebar-dropdown").removeClass("active");
      $(this).next(".sidebar-submenu").slideDown(200);
      $(this).parent().addClass("active");
    }
  });

  $("#close-sidebar").click(function () {
    $(".page-wrapper").removeClass("toggled");
  });

  $("#show-sidebar").click(function () {
    $(".page-wrapper").addClass("toggled");
  });

  // Close sidebar on small screens
  function handleSidebarState() {
    if ($(window).width() <= 991) {
      $(".page-wrapper").removeClass("toggled");
    }
  }

  // Check on page load
  handleSidebarState();

  // Check on window resize
  $(window).resize(function () {
    handleSidebarState();
  });
});


